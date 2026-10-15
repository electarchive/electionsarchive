with recursive
rs as (
  select r.k, e.date_from as on_date from result_set r join election e on e.id = r.election_id where r.k = :rs
),
units as (
  select ru.k as unit_k, ru.id as unit_id, coalesce(ru.area_id, pt.area_id) as area_id
  from reporting_unit ru
  join (select distinct unit_k from result_value where result_set_k = :rs) v on v.unit_k = ru.k
  cross join rs
  left join polity_territory pt on pt.polity_id = ru.polity_id and pt.valid @> rs.on_date
),
ancestors as (
  select unit_k, area_id from units
  union
  select u.unit_k, ua.area_id from units u cross join rs
  join reporting_unit_area ua on ua.unit_id = u.unit_id and ua.valid @> rs.on_date
  union
  select a.unit_k, p.parent_id from ancestors a cross join rs
  join area_parent p on p.area_id = a.area_id and p.valid @> rs.on_date
)
insert into result_rollup (result_set_k, area_k, option_k, channel, measure, count_round, exact, value)
select v.result_set_k, area.k, v.option_k, v.channel, v.measure, v.count_round, true, sum(v.value)
from result_value v join ancestors a on a.unit_k = v.unit_k join area on area.id = a.area_id
where v.result_set_k = :rs
group by v.result_set_k, area.k, v.option_k, v.channel, v.measure, v.count_round;
