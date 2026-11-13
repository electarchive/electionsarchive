create table live.ballots (
  fetch_id bigint not null references live.fetch (id) on delete cascade,
  election_id uuid not null,
  unit_id uuid not null,
  as_of timestamptz not null,
  stage text not null check (stage in ('requested', 'issued', 'returned', 'accepted', 'rejected', 'remaining')),
  channel smallint not null default 0 references public.channel (id),
  count numeric not null check (count >= 0),
  primary key (fetch_id, unit_id, stage, channel, as_of)
);--> statement-breakpoint
create index live_ballots_election_idx on live.ballots (election_id, unit_id, stage, channel, as_of desc);--> statement-breakpoint
do $$ begin
  if exists (select from pg_roles where rolname = 'ea_reader') then grant select on live.ballots to ea_reader; end if;
  if exists (select from pg_roles where rolname = 'ea_poll') then grant select, insert, delete on live.ballots to ea_poll; end if;
end $$;
