create table live.expectation (
  frame_k bigint not null references live.frame (k) on delete cascade,
  unit_id uuid not null,
  share numeric not null check (share > 0 and share <= 1),
  expected numeric not null check (expected > 0),
  primary key (frame_k, unit_id)
);--> statement-breakpoint
do $$ begin
  if exists (select from pg_roles where rolname = 'ea_reader') then grant select on live.expectation to ea_reader; end if;
  if exists (select from pg_roles where rolname = 'ea_poll') then grant select, insert, delete on live.expectation to ea_poll; end if;
end $$;
