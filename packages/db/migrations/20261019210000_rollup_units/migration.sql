create temporary table rollup_single on commit drop as
select result_set_k, min(unit_k) as unit_k from result_value group by result_set_k having count(distinct unit_k) = 1;
--> statement-breakpoint
analyze rollup_single;
--> statement-breakpoint
create temporary table rollup_candidate on commit drop as
select distinct s.result_set_k, r.area_k, s.unit_k
from rollup_single s join result_rollup r on r.result_set_k = s.result_set_k
union
select o.result_set_k, o.area_k, o.unit_k
from (
  select u.result_set_k, a.k as area_k, min(u.unit_k) as unit_k
  from (select distinct result_set_k, unit_k from result_value) u
  join reporting_unit ru on ru.k = u.unit_k
  join area a on a.id = ru.area_id
  group by u.result_set_k, a.k
  having count(*) = 1
) o;
--> statement-breakpoint
analyze rollup_candidate;
--> statement-breakpoint
create temporary table rollup_mismatch on commit drop as
select distinct coalesce(r.result_set_k, v.result_set_k) as result_set_k, coalesce(r.area_k, v.area_k) as area_k
from (select r.* from result_rollup r join rollup_candidate c on c.result_set_k = r.result_set_k and c.area_k = r.area_k) r
full join (select c.area_k, v.* from rollup_candidate c join result_value v on v.result_set_k = c.result_set_k and v.unit_k = c.unit_k) v
  on v.result_set_k = r.result_set_k and v.area_k = r.area_k and v.option_k = r.option_k and v.channel = r.channel
  and v.measure = r.measure and v.count_round = r.count_round
where r.result_set_k is null or v.result_set_k is null or r.value <> v.value or not r.complete;
--> statement-breakpoint
create table result_rollup_unit (result_set_k integer not null, area_k integer not null, unit_k integer not null, primary key (result_set_k, area_k));
--> statement-breakpoint
insert into result_rollup_unit
select c.result_set_k, c.area_k, c.unit_k from rollup_candidate c
where exists (select from result_rollup r where r.result_set_k = c.result_set_k and r.area_k = c.area_k)
  and not exists (select from rollup_mismatch m where m.result_set_k = c.result_set_k and m.area_k = c.area_k)
order by 1, 2;
--> statement-breakpoint
create table result_rollup_sum (result_set_k integer not null, area_k integer not null, option_k integer default 0 not null, channel smallint default 0 not null, measure smallint not null, count_round smallint default 0 not null, complete boolean not null, value numeric not null);
--> statement-breakpoint
insert into result_rollup_sum select r.* from result_rollup r
where not exists (select from result_rollup_unit u where u.result_set_k = r.result_set_k and u.area_k = r.area_k)
order by r.result_set_k, r.area_k, r.option_k, r.channel, r.measure, r.count_round;
--> statement-breakpoint
alter table result_rollup_sum add constraint result_rollup_sum_pkey primary key (result_set_k, area_k, option_k, channel, measure, count_round);
--> statement-breakpoint
drop table result_rollup;
--> statement-breakpoint
comment on column result_rollup_sum.result_set_k is 'references result_set (k)';
--> statement-breakpoint
comment on column result_rollup_sum.area_k is 'references area (k)';
--> statement-breakpoint
comment on column result_rollup_sum.option_k is 'references contest_option (k)';
--> statement-breakpoint
comment on column result_rollup_unit.result_set_k is 'references result_set (k)';
--> statement-breakpoint
comment on column result_rollup_unit.area_k is 'references area (k)';
--> statement-breakpoint
comment on column result_rollup_unit.unit_k is 'references reporting_unit (k)';
--> statement-breakpoint
create view result_rollup as
select result_set_k, area_k, option_k, channel, measure, count_round, complete, value from result_rollup_sum
union all
select u.result_set_k, u.area_k, v.option_k, v.channel, v.measure, v.count_round, true, v.value
from result_rollup_unit u join result_value v on v.result_set_k = u.result_set_k and v.unit_k = u.unit_k;
