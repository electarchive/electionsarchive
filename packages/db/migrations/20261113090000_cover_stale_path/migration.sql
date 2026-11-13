create or replace function ea_cover_stale() returns trigger language plpgsql as $$
begin
  if new.contest_id is null then
    delete from public.contest_cover;
  else
    delete from public.contest_cover where id = new.contest_id;
  end if;
  return null;
end $$;
