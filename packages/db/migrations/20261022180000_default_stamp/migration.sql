set lock_timeout = '5s';

create function ea_stamp_result_set() returns trigger language plpgsql as $$
begin
  if tg_op = 'UPDATE' and old.is_default is distinct from new.is_default
     and to_jsonb(old) - 'is_default' - 'changeset_id' - 'recorded_from'
       = to_jsonb(new) - 'is_default' - 'changeset_id' - 'recorded_from' then
    new.changeset_id := old.changeset_id;
  else
    new.changeset_id := ea_current_changeset();
  end if;
  new.recorded_from := now();
  return new;
end $$;

drop trigger ea_stamp on result_set;
create trigger ea_stamp before insert or update on result_set for each row execute function ea_stamp_result_set();
