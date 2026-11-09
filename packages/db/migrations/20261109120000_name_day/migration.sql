create index if not exists name_bounded_idx on name (valid) where not (lower_inf(valid) and upper_inf(valid));
create or replace function name_day() returns date language sql stable parallel safe as $$
  select coalesce(max(b), '-infinity'::date)
  from name, lateral (values (lower(valid)), (upper(valid))) v(b)
  where not (lower_inf(valid) and upper_inf(valid)) and b <= current_date
$$;
