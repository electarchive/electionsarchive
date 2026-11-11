do $$
begin
  if (select indnatts = indnkeyatts from pg_index where indexrelid = 'result_rollup_unit_pkey'::regclass) then
    create unique index result_rollup_unit_cover on result_rollup_unit (result_set_k, area_k) include (unit_k);
    create index result_rollup_unit_area_k_cover on result_rollup_unit (area_k, result_set_k) include (unit_k);
    alter table result_rollup_unit drop constraint result_rollup_unit_pkey, add constraint result_rollup_unit_pkey primary key using index result_rollup_unit_cover;
    drop index result_rollup_unit_area_k_idx;
    alter index result_rollup_unit_area_k_cover rename to result_rollup_unit_area_k_idx;
  end if;
end $$;
