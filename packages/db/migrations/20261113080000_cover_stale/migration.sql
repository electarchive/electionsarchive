create table cover_stale (
  k bigint generated always as identity primary key,
  contest_id uuid,
  changeset_id uuid,
  at timestamptz not null default now()
);
--> statement-breakpoint
create index cover_stale_at on cover_stale (at);
--> statement-breakpoint
create function ea_cover_stale() returns trigger language plpgsql as $$
begin
  if new.contest_id is null then
    delete from contest_cover;
  else
    delete from contest_cover where id = new.contest_id;
  end if;
  return null;
end $$;
--> statement-breakpoint
create trigger ea_cover_stale after insert on cover_stale for each row execute function ea_cover_stale();
--> statement-breakpoint
alter table cover_stale enable replica trigger ea_cover_stale;
