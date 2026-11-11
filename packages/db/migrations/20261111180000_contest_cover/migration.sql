create table if not exists contest_cover (
  id uuid primary key,
  series text,
  election uuid,
  indirect boolean,
  date text,
  year integer,
  status text,
  is_remainder boolean,
  notional boolean,
  district uuid,
  members uuid[],
  decided boolean,
  reach text[],
  built_at timestamptz not null default now()
);--> statement-breakpoint
create index if not exists contest_cover_district_idx on contest_cover (district) where district is not null;--> statement-breakpoint
do $$ begin
  if exists (select from pg_roles where rolname = 'ea_reader') then grant select on contest_cover to ea_reader; end if;
end $$;
