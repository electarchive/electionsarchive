\timing off
\pset footer off
vacuum analyze;

select r.k as rs, r.id as rs_id from result_set r join name n on n.entity_id = r.contest_id
where n.text = '2022 presidential election, round 1' \gset

\echo '== fact table: rows, heap, and index size'
select count(*) as rows, pg_size_pretty(sum(pg_table_size(c.oid))) as heap, pg_size_pretty(sum(pg_indexes_size(c.oid))) as indexes
from pg_inherits i join pg_class c on c.oid = i.inhrelid
where i.inhparent = 'result_value'::regclass;
select (select count(*) from result_value) as total_rows;

\echo '== partition skew (hash on result_set_k, 32 partitions)'
select c.relname, c.reltuples::bigint as rows from pg_inherits i join pg_class c on c.oid = i.inhrelid
where i.inhparent = 'result_value'::regclass order by 2 desc limit 4;

drop table if exists bench_numeric, bench_int8, bench_uuid;
create unlogged table bench_numeric as select * from result_value;
alter table bench_numeric add primary key (result_set_k, unit_k, option_k, channel, measure, count_round);
create unlogged table bench_int8 as
  select result_set_k, unit_k, option_k, channel, measure, count_round, flags, value::int8 as value from result_value;
alter table bench_int8 add primary key (result_set_k, unit_k, option_k, channel, measure, count_round);
create unlogged table bench_uuid as
  select r.id as result_set_id, u.id as unit_id, coalesce(o.id, '00000000-0000-0000-0000-000000000000'::uuid) as option_id,
         v.channel, v.measure, v.count_round, v.flags, v.value
  from result_value v join result_set r on r.k = v.result_set_k join reporting_unit u on u.k = v.unit_k
  left join contest_option o on o.k = v.option_k;
alter table bench_uuid add primary key (result_set_id, unit_id, option_id, channel, measure, count_round);
vacuum analyze bench_numeric, bench_int8, bench_uuid;

\echo '== storage by variant'
select t, pg_size_pretty(pg_table_size(t::regclass)) as heap, pg_size_pretty(pg_indexes_size(t::regclass)) as pk,
       round(pg_table_size(t::regclass)::numeric / (select count(*) from bench_numeric), 1) as heap_bytes_per_row
from unnest(array['bench_numeric', 'bench_int8', 'bench_uuid']) as t;

\timing on
\echo '== Q1 national totals of one result set (5M rows), warm, numeric / int8 / uuid / partitioned'
select option_k, sum(value) from bench_numeric where result_set_k = :rs group by 1 \g /dev/null
select option_k, sum(value) from bench_numeric where result_set_k = :rs group by 1 \g /dev/null
select option_k, sum(value) from bench_int8 where result_set_k = :rs group by 1 \g /dev/null
select option_k, sum(value) from bench_int8 where result_set_k = :rs group by 1 \g /dev/null
select option_id, sum(value) from bench_uuid where result_set_id = :'rs_id' group by 1 \g /dev/null
select option_id, sum(value) from bench_uuid where result_set_id = :'rs_id' group by 1 \g /dev/null
select option_k, sum(value) from result_value where result_set_k = :rs group by 1 \g /dev/null
select option_k, sum(value) from result_value where result_set_k = :rs group by 1 \g /dev/null

\echo '== Q2 municipality rollup of one result set (join to units), numeric / int8 / uuid'
select ru.area_id, v.option_k, sum(v.value) from bench_numeric v join reporting_unit ru on ru.k = v.unit_k
where v.result_set_k = :rs group by 1, 2 \g /dev/null
select ru.area_id, v.option_k, sum(v.value) from bench_int8 v join reporting_unit ru on ru.k = v.unit_k
where v.result_set_k = :rs group by 1, 2 \g /dev/null
select ru.area_id, v.option_id, sum(v.value) from bench_uuid v join reporting_unit ru on ru.id = v.unit_id
where v.result_set_id = :'rs_id' group by 1, 2 \g /dev/null

\echo '== Q3 one unit, all values (point lookup), numeric / uuid / partitioned'
select unit_k as probe_k from bench_numeric where result_set_k = :rs limit 1 offset 1000000 \gset
select id as probe_id from reporting_unit where k = :probe_k \gset
select * from bench_numeric where result_set_k = :rs and unit_k = :probe_k \g /dev/null
select * from bench_uuid where result_set_id = :'rs_id' and unit_id = :'probe_id' \g /dev/null
select * from result_value where result_set_k = :rs and unit_k = :probe_k \g /dev/null

\echo '== Q4 correction by full copy: duplicate the 5M-row result set under a new key'
begin;
insert into bench_numeric select 999999, unit_k, option_k, channel, measure, count_round, flags, value
from bench_numeric where result_set_k = :rs;
rollback;

\echo '== Q5 rollup rebuild for one result set (5M rows to municipality, state, nation)'
begin;
delete from result_rollup where result_set_k = :rs;
\i spike/rollup_one.sql
rollback;

\timing off
\echo '== history trigger overhead: 100k name inserts and updates, versioned vs plain'
begin;
select set_config('ea.changeset_id', (select id::text from changeset limit 1), true) \g /dev/null
create temp table name_plain (like name including defaults);
\timing on
insert into name (entity_id, text, lang, kind) select entity_id, text || ' bench', lang, kind from name limit 100000;
insert into name_plain (entity_id, text, lang, kind, changeset_id) select entity_id, text || ' bench', lang, kind, changeset_id from name limit 100000;
update name set text = text || '!' where text like '% bench';
update name_plain set text = text || '!';
\timing off
select count(*) as history_rows from name_history;
rollback;

drop table bench_numeric, bench_int8, bench_uuid;
