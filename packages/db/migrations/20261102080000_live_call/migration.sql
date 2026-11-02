create table live.call (
  contest_id uuid primary key,
  option_id uuid not null,
  called_at timestamptz not null default now(),
  chance double precision check (chance between 0 and 1),
  counted double precision check (counted between 0 and 1),
  margin double precision
);--> statement-breakpoint
do $$ begin
  if exists (select from pg_roles where rolname = 'ea_reader') then grant select on live.call to ea_reader; end if;
end $$;
