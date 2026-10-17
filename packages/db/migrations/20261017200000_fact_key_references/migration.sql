comment on column result_value.result_set_k is 'references result_set (k)';
comment on column result_value.unit_k is 'references reporting_unit (k)';
comment on column result_value.option_k is 'references contest_option (k)';
comment on column result_flow.result_set_k is 'references result_set (k)';
comment on column result_flow.unit_k is 'references reporting_unit (k)';
comment on column result_flow.from_option_k is 'references contest_option (k)';
comment on column result_flow.option_k is 'references contest_option (k)';
comment on column value_locator.result_set_k is 'references result_set (k)';
comment on column value_locator.unit_k is 'references reporting_unit (k)';
comment on column value_locator.option_k is 'references contest_option (k)';
comment on column result_rollup.result_set_k is 'references result_set (k)';
comment on column result_rollup.area_k is 'references area (k)';
comment on column result_rollup.option_k is 'references contest_option (k)';

create table fact_rekey (
  changeset_id uuid not null references changeset (id),
  relation text not null,
  key_column text not null,
  from_k int4 not null,
  to_k int4 not null,
  row_key jsonb not null
);
create index fact_rekey_changeset_idx on fact_rekey (changeset_id);
