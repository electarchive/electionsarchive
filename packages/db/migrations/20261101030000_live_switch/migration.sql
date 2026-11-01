create table live.switch (
  name text primary key check (name in ('results', 'forecasts')),
  shown boolean not null,
  changed_at timestamptz not null default now()
);--> statement-breakpoint
insert into live.switch (name, shown) values ('results', false), ('forecasts', false);--> statement-breakpoint
do $$ begin
  if exists (select from pg_roles where rolname = 'ea_reader') then grant select on live.switch to ea_reader; end if;
  if exists (select from pg_roles where rolname = 'ea_poll') then grant select, update on live.switch to ea_poll; end if;
end $$;
