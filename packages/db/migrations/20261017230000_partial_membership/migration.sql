alter table reporting_unit_area add column partial boolean not null default false;

alter table reporting_unit_area_history rename to reporting_unit_area_history_before;
create table reporting_unit_area_history (like reporting_unit_area, recorded tstzrange not null, superseded_by uuid not null);
insert into reporting_unit_area_history (unit_id, area_id, valid_from, valid_from_precision, valid_to, valid_to_precision,
                                         valid, changeset_id, recorded_from, approximate, partial, recorded, superseded_by)
select unit_id, area_id, valid_from, valid_from_precision, valid_to, valid_to_precision, valid, changeset_id,
       recorded_from, approximate, false, recorded, superseded_by
from reporting_unit_area_history_before;
drop table reporting_unit_area_history_before;
