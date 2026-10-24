alter table area_universe add column shaped boolean;
--> statement-breakpoint
alter table area_universe_history rename to area_universe_history_before;
--> statement-breakpoint
create table area_universe_history (like area_universe, recorded tstzrange not null, superseded_by uuid not null);
--> statement-breakpoint
insert into area_universe_history (area_id, valid_from, valid_from_precision, valid_to, valid_to_precision, valid, expected, method,
                                   source_id, result_set_id, changeset_id, recorded_from, recorded, superseded_by)
select area_id, valid_from, valid_from_precision, valid_to, valid_to_precision, valid, expected, method,
       source_id, result_set_id, changeset_id, recorded_from, recorded, superseded_by
from area_universe_history_before;
--> statement-breakpoint
drop table area_universe_history_before;
