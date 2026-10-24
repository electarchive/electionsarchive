alter table area_universe disable trigger user;
--> statement-breakpoint
analyze area_universe;
--> statement-breakpoint
update area_universe u set shaped = s.shaped
from (
  with members as materialized (
    select ua.area_id, ua.unit_id, ua.valid from reporting_unit_area ua
    where not ua.approximate and ua.area_id in (select area_id from area_universe)
  )
  select au.area_id, au.valid,
         count(*) filter (where own.scheme_code = ru.scheme_code) * 2 >= count(*) as shaped
  from area_universe au
  join members m on m.area_id = au.area_id and m.valid @> lower(au.valid)
  join reporting_unit ru on ru.id = m.unit_id and ru.scheme_code = 'us.precinct' and lower(ru.valid) = lower(au.valid)
  left join area own on own.id = ru.area_id
  where au.shaped is null
  group by au.area_id, au.valid
) s
where u.area_id = s.area_id and u.valid = s.valid and u.shaped is null;
--> statement-breakpoint
update area_universe set shaped = true where shaped is null;
--> statement-breakpoint
alter table area_universe enable trigger user;
--> statement-breakpoint
alter table area_universe alter column shaped set not null;
--> statement-breakpoint
alter table area_universe drop constraint area_universe_pkey;
--> statement-breakpoint
alter table area_universe add primary key (area_id, shaped, valid without overlaps);
--> statement-breakpoint
alter table area_universe_history rename to area_universe_history_before;
--> statement-breakpoint
create table area_universe_history (like area_universe, recorded tstzrange not null, superseded_by uuid not null);
--> statement-breakpoint
alter table area_universe_history alter column shaped drop not null;
--> statement-breakpoint
insert into area_universe_history (area_id, valid_from, valid_from_precision, valid_to, valid_to_precision, valid, expected, method,
                                   source_id, result_set_id, changeset_id, recorded_from, shaped, recorded, superseded_by)
select area_id, valid_from, valid_from_precision, valid_to, valid_to_precision, valid, expected, method,
       source_id, result_set_id, changeset_id, recorded_from, shaped, recorded, superseded_by
from area_universe_history_before;
--> statement-breakpoint
drop table area_universe_history_before;
